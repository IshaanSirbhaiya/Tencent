"""Load the SYNTHETIC finance estate (core_banking + crm) into its own database inside the
existing demo Postgres container, side by side with the Larkspur demo.

    python scripts/finance_live/load_finance_db.py            # create + load if needed, then verify
    python scripts/finance_live/load_finance_db.py --verify   # verify only
    python scripts/finance_live/load_finance_db.py --clean    # drop finance_demo and reload it

What it touches (and nothing else):
  * database  finance_demo            (new; the Larkspur database `larkspur` is never touched)
  * schemas   core_banking, crm       (from fixtures/packs/finance/<source>/schema.sql + seed/*.sql)
  * schema    ops + table ops.follow_up_task   (the agent-write target, mirrors Larkspur's
                                                public.follow_up_tasks)
  * roles     finance_reader          LOGIN, SELECT on core_banking/crm/ops only, CONNECT finance_demo
              finance_agent_writer    LOGIN, INSERT on ops.follow_up_task only
  PUBLIC loses CONNECT on finance_demo, so the Larkspur roles cannot reach it either.

Everything is synthetic. The two role passwords are the synthetic demo values the launcher sets
(WALNUT_FIN_PG_READER_PASSWORD / WALNUT_FIN_PG_WRITER_PASSWORD); they are never printed.

It talks to Postgres with `docker exec -i <container> psql`, so no psql is needed on the host. The
seed's own `\\i` load order (load.sql) is followed: the files are streamed in that order.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]                       # databridge/
ESTATE = ROOT / "fixtures" / "packs" / "finance"
CONTAINER = os.environ.get("WALNUT_DEMO_PG_CONTAINER", "walnut_demo_pg")
SUPERUSER = os.environ.get("WALNUT_DEMO_PG_USER", "walnut")
DB = os.environ.get("WALNUT_FIN_PG_DB", "finance_demo")
READER = "finance_reader"
WRITER = "finance_agent_writer"
SOURCES = ("core_banking", "crm")


def psql(sql: str, *, db: str = "postgres", quiet: bool = True, check: bool = True) -> str:
    cmd = ["docker", "exec", "-i", CONTAINER, "psql", "-U", SUPERUSER, "-d", db,
           "-v", "ON_ERROR_STOP=1", "-X", "-A", "-t"]
    if quiet:
        cmd.append("-q")
    r = subprocess.run(cmd, input=sql.encode("utf-8"), capture_output=True)
    if check and r.returncode != 0:
        # never echo the input (it may carry a password); stderr from psql does not
        sys.exit(f"psql failed on {db}: {r.stderr.decode('utf-8', 'replace')[:2000]}")
    return r.stdout.decode("utf-8", "replace").strip()


def load_order(source: str) -> list[Path]:
    """The files load.sql includes, in its order (schema.sql first)."""
    base = ESTATE / source
    out = []
    for line in (base / "load.sql").read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*\\i\s+(\S+)", line)
        if m:
            out.append(base / m.group(1))
    if not out or out[0].name != "schema.sql":
        sys.exit(f"{source}/load.sql: unexpected shape (schema.sql must come first)")
    return out


def lit(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def ensure_roles() -> None:
    rp = os.environ.get("WALNUT_FIN_PG_READER_PASSWORD")
    wp = os.environ.get("WALNUT_FIN_PG_WRITER_PASSWORD")
    if not rp or not wp:
        sys.exit("set WALNUT_FIN_PG_READER_PASSWORD and WALNUT_FIN_PG_WRITER_PASSWORD "
                 "(start-walnut-finance.ps1 sets the synthetic demo values)")
    for role, pw in ((READER, rp), (WRITER, wp)):
        psql(f"""DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {lit(role)}) THEN
    CREATE ROLE {role} LOGIN PASSWORD {lit(pw)};
  ELSE
    ALTER ROLE {role} LOGIN PASSWORD {lit(pw)};
  END IF;
END $$;""")


def db_exists() -> bool:
    return psql(f"SELECT 1 FROM pg_database WHERE datname = {lit(DB)};") == "1"


def load() -> None:
    psql(f"CREATE DATABASE {DB};")
    psql(f"REVOKE CONNECT ON DATABASE {DB} FROM PUBLIC;")
    for source in SOURCES:
        files = load_order(source)
        body = "\n".join(p.read_text(encoding="utf-8") for p in files)
        print(f"  loading {source}: {len(files)} files ...", flush=True)
        psql(body, db=DB)
    psql("""
CREATE SCHEMA IF NOT EXISTS ops;
COMMENT ON SCHEMA ops IS 'SYNTHETIC - agent write target for the finance live demo';
CREATE TABLE IF NOT EXISTS ops.follow_up_task (
    task_id      BIGSERIAL PRIMARY KEY,
    subject_ref  VARCHAR(64)  NOT NULL,
    note         TEXT         NOT NULL,
    due_on       DATE,
    created_by   VARCHAR(120) NOT NULL,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    evidence     TEXT
);
""", db=DB)


def grant() -> None:
    psql(f"GRANT CONNECT ON DATABASE {DB} TO {READER}, {WRITER};")
    psql(f"""
GRANT USAGE ON SCHEMA core_banking, crm, ops TO {READER};
GRANT SELECT ON ALL TABLES IN SCHEMA core_banking, crm, ops TO {READER};
GRANT USAGE ON SCHEMA ops TO {WRITER};
GRANT INSERT ON ops.follow_up_task TO {WRITER};
GRANT USAGE ON SEQUENCE ops.follow_up_task_task_id_seq TO {WRITER};
""", db=DB)


def verify() -> bool:
    expected = json.loads((ESTATE / "ground_truth" / "expected_counts.json")
                          .read_text(encoding="utf-8"))["counts"]
    ok = True
    for key, want in sorted(expected.items()):
        schema = key.split(".")[0]
        if schema not in SOURCES:
            continue
        got = psql(f"SELECT count(*) FROM {key};", db=DB)
        flag = "ok" if str(want) == got else "MISMATCH"
        ok &= flag == "ok"
        print(f"  {key:45s} {got:>7s}  (expected {want})  {flag}")
    # the read-only role really is read-only, and really can read
    rp = os.environ.get("WALNUT_FIN_PG_READER_PASSWORD", "")
    probe = subprocess.run(
        ["docker", "exec", "-i", "-e", f"PGPASSWORD={rp}", CONTAINER, "psql", "-h", "127.0.0.1",
         "-U", READER, "-d", DB, "-X", "-A", "-t", "-q"],
        input=b"SELECT count(*) FROM crm.relationship_manager;\n"
              b"INSERT INTO ops.follow_up_task(subject_ref, note, created_by) VALUES ('x','x','x');\n",
        capture_output=True)
    out, err = probe.stdout.decode().strip(), probe.stderr.decode()
    can_read = out.splitlines()[:1] == [str(expected["crm.relationship_manager"])]
    cannot_write = "permission denied" in err
    print(f"  {READER}: can read={can_read}  insert refused={cannot_write}")
    larkspur = subprocess.run(
        ["docker", "exec", "-i", "-e", "PGPASSWORD", CONTAINER, "psql", "-h", "127.0.0.1",
         "-U", "walnut_reader",
         "-d", DB, "-X", "-c", "select 1"], capture_output=True,
        env={**os.environ, "PGPASSWORD": os.environ.get("WALNUT_DEMO_PG_READER_PASSWORD", "")})
    print(f"  walnut_reader (Larkspur) can connect to {DB}: {larkspur.returncode == 0}")
    return ok and can_read and cannot_write


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clean", action="store_true", help=f"drop {DB} first and reload it")
    ap.add_argument("--verify", action="store_true", help="verify only")
    a = ap.parse_args()
    if a.verify:
        sys.exit(0 if verify() else 1)
    ensure_roles()
    if a.clean and db_exists():
        print(f"  dropping {DB} (finance only; larkspur untouched)")
        psql(f"DROP DATABASE {DB} WITH (FORCE);")
    if db_exists():
        print(f"  {DB} already exists - not reloading (use --clean to rebuild)")
    else:
        load()
    grant()
    if not verify():
        sys.exit("finance_demo verification FAILED")
    print(f"finance_demo ready: postgresql://{READER}:***@127.0.0.1:5433/{DB}")


if __name__ == "__main__":
    main()
