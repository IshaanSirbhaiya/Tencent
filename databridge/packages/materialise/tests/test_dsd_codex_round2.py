"""R-027 DSD — codex round 2 findings, written FIRST (each failed before its fix), both estates.

1 (Critical) the sealed design log permitted ROLLBACK: truncating its tail after an approval
             resurrected the stale approved revision.
2 (Critical) TOCTOU: an edit could land between the final approval check and publication.
3 (Critical) pointers could go stale after the live check (a source moving before publication).
4 (High)     a SUBJECT grain's rows were emitted: the per-row subject test was computed and dropped.
5 (High)     build reuse ignored pointer provenance (same values, new source snapshot → old sidecar).
6 (Medium)   the design lock was process-local.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import dsd_estates as de
from test_dsd_delegated_datasets import _env, _proposed, _receipt, estate  # noqa: F401


def _adm():
    from databridge_federated import CatalogueAdmission
    return CatalogueAdmission("staff")


def _approved(estate, **kw):
    m, svc, doc, _ = _proposed(estate, model=False, **kw)
    doc = m.approve_design(doc["id"], digest=doc["digest"], principal=de.issued_principal(),
                           by="staff")
    return m, svc, doc


# ------------------------------------------------------------------------------------------------
# 1. rollback by tail truncation
# ------------------------------------------------------------------------------------------------
def test_r2_1_a_truncated_log_cannot_roll_back_to_an_old_approval(estate):  # noqa: F811
    from databridge_materialise.delegated import DesignRefused
    m, svc, ok = _approved(estate)
    log = m.out_root / "designs" / ok["id"] / "events.jsonl"
    at_approval = log.read_bytes()
    m.edit_design(ok["id"], {"columns": ok["design"]["columns"][:-2]}, by="staff", service=svc,
                  rev=ok["rev"])
    assert m.design(ok["id"], by="staff")["status"] == "edited"
    log.write_bytes(at_approval)                     # a valid sealed PREFIX: the old approval
    v = m.design(ok["id"], by="staff")
    assert v is None or v["status"] == "integrity_failed"
    with pytest.raises(DesignRefused) as e:
        m.build_design(ok["id"], digest=ok["digest"], by="staff", service=svc, admission=_adm(), actor=de.build_actor())
    assert e.value.code in ("DESIGN_INTEGRITY_FAILED", "DESIGN_NOT_FOUND")


# ------------------------------------------------------------------------------------------------
# 2. publication is one transaction: an edit waits for it
# ------------------------------------------------------------------------------------------------
def test_r2_2_an_edit_cannot_land_inside_the_publication_window(estate, monkeypatch):  # noqa: F811
    from databridge_materialise.platform import PlatformMaterialiser
    m, svc, ok = _approved(estate)
    real = PlatformMaterialiser._publish
    seen: dict = {}

    def publish(self, staging, final, doc):
        t = threading.Thread(target=lambda: seen.setdefault("edit", m.edit_design(
            ok["id"], {"columns": ok["design"]["columns"][:-2]}, by="staff", service=svc,
            rev=ok["rev"])))
        t.start()
        t.join(1.5)
        seen["edit_waited"] = t.is_alive()           # still blocked by the publication lock
        seen["thread"] = t
        return real(self, staging, final, doc)
    monkeypatch.setattr(PlatformMaterialiser, "_publish", publish)
    res = m.build_design(ok["id"], digest=ok["digest"], by="staff", service=svc,
                         admission=_adm(), actor=de.build_actor())
    seen["thread"].join(60)
    assert seen["edit_waited"], "the edit committed inside the publication window"
    hist = [h["action"] for h in m.design(ok["id"], by="staff")["history"]]
    assert hist[-2:] == ["built", "edited"], hist      # the build is recorded BEFORE the edit
    assert res["design_digest"] == ok["digest"]


# ------------------------------------------------------------------------------------------------
# 3. a source moving after the pointer check is caught at publication
# ------------------------------------------------------------------------------------------------
def test_r2_3_a_source_that_moves_before_publication_refuses_it(estate, monkeypatch):  # noqa: F811
    import sqlite3

    import databridge_materialise.platform as plat
    from databridge_materialise.delegated import DesignRefused
    from databridge_materialise.platform import RunRefused
    from databridge_mcp_ingest.tabular.facts import sha256_file
    m, svc, ok = _approved(estate)
    facts = estate.root / "facts" / f"{estate.db}_facts.sqlite"
    manifest = facts.with_name(f"{estate.db}_facts.json")
    before, mbefore = facts.read_bytes(), manifest.read_bytes()
    real = plat.write_pointer_sidecar

    def move_then_write(*a, **k):
        con = sqlite3.connect(facts)
        grain = ok["design"]["grain"]["entity"]
        key = ok["design"]["grain_key"]
        con.execute(f'UPDATE "{grain}" SET "{key}" = "{key}" || \'-moved\'')
        con.commit()
        con.close()
        mm = json.loads(mbefore)
        mm["sha256"] = sha256_file(facts)
        manifest.write_text(json.dumps(mm), encoding="utf-8")
        return real(*a, **k)
    monkeypatch.setattr(plat, "write_pointer_sidecar", move_then_write)
    published = {p.name for p in m.out_root.iterdir()}
    try:
        with pytest.raises((DesignRefused, RunRefused)) as e:
            m.build_design(ok["id"], digest=ok["digest"], by="staff", service=svc,
                           admission=_adm(), actor=de.build_actor())
        assert e.value.code in ("SOURCE_MOVED", "POINTER_CHECK_FAILED")
        assert {p.name for p in m.out_root.iterdir()} - published <= {
            n for n in {p.name for p in m.out_root.iterdir()} if n.startswith(".")}
    finally:
        facts.write_bytes(before)
        manifest.write_bytes(mbefore)


# ------------------------------------------------------------------------------------------------
# 4. a subject grain's person rows are withheld (per-row split applied)
# ------------------------------------------------------------------------------------------------
SUBJECT_GRAIN = {"workspace": ("employees", "employee data with title and office"),
                 "finance": ("party", "party data with risk grade and country")}


def test_r2_4_subject_rows_of_the_grain_are_withheld(estate):  # noqa: F811
    from databridge_graph.packs import active_vocabulary
    from databridge_mcp_ingest.mapping import split_value
    from databridge_materialise.delegated import CANDIDATE_REASON, RECORD_TIME
    grain, request = SUBJECT_GRAIN[estate.name]
    m, svc, ok = _approved(estate, grain=grain, request=request)
    assert ok["design"]["grain"]["entity"] == grain
    res = m.build_design(ok["id"], digest=ok["digest"], by="staff", service=svc,
                         admission=_adm(), actor=de.build_actor())
    rec = _receipt(m, res)["receipt"]
    side = m.out_root / res["plan_hash"] / rec["pointer_manifest"]["uri"]
    cells = [json.loads(x) for x in side.read_text("utf-8").splitlines()]
    key = ok["design"]["grain_key"]
    v = active_vocabulary()
    tm = next(t for r, t in estate.mapping.tables.items() if r.endswith(f".{grain}"))
    split = tm.split
    # the dataset is ordered by the row key (as text); the key itself may be a masked identifier
    ordered = sorted(estate.rows[grain], key=lambda r: str(r[key]))
    persons = 0
    for c in cells:
        if c["column"] in (key, RECORD_TIME):
            continue
        src = ordered[c["row"]]
        kind = ((split["kinds"].get(split_value(src[split["column"]])) or split["default"])
                if split else tm.node["kind"])
        if v.is_subject(kind):
            persons += 1
            assert c["state"] == "withheld", c
            assert c["reason_code"] in (CANDIDATE_REASON, "direct_identifier_masked")
        else:
            assert c["state"] in ("grounded", "absent")
    assert persons > 0, "the estate's grain holds person rows"


# ------------------------------------------------------------------------------------------------
# 5. reuse compares pointer provenance
# ------------------------------------------------------------------------------------------------
def test_r2_5_a_new_source_snapshot_is_a_new_build(estate):  # noqa: F811
    import sqlite3

    from databridge_mcp_ingest.tabular.facts import sha256_file
    m, svc, ok = _approved(estate)
    first = m.build_design(ok["id"], digest=ok["digest"], by="staff", service=svc,
                           admission=_adm(), actor=de.build_actor())
    facts = estate.root / "facts" / f"{estate.db}_facts.sqlite"
    manifest = facts.with_name(f"{estate.db}_facts.json")
    before, mbefore = facts.read_bytes(), manifest.read_bytes()
    try:
        con = sqlite3.connect(facts)             # same VALUES, a new snapshot of the store
        con.execute("INSERT INTO _facts_meta VALUES ('note', 'a later extraction')")
        con.commit()
        con.close()
        mm = json.loads(mbefore)
        mm["sha256"] = sha256_file(facts)
        manifest.write_text(json.dumps(mm), encoding="utf-8")
        second = m.build_design(ok["id"], digest=ok["digest"], by="staff",
                                service=estate.service(), admission=_adm(), actor=de.build_actor())
        assert second["content_hash"] == first["content_hash"]
        assert second["plan_hash"] != first["plan_hash"]
        assert not second["reused_prior_run"]
        rec2 = _receipt(m, second)
        side = m.out_root / second["plan_hash"] / rec2["receipt"]["pointer_manifest"]["uri"]
        assert sha256_file(facts) in side.read_text("utf-8")
        assert rec2["platform"]["pointer_file"]["sha256"] == __import__("hashlib").sha256(
            side.read_bytes()).hexdigest()
    finally:
        facts.write_bytes(before)
        manifest.write_bytes(mbefore)


# ------------------------------------------------------------------------------------------------
# 6. the lock holds across PROCESSES
# ------------------------------------------------------------------------------------------------
_HOLDER = r'''
import sys, time
sys.path[:0] = {paths!r}
from databridge_materialise.delegated import DesignStore
from pathlib import Path
s = DesignStore(Path({root!r}))
with s.transaction({did!r}):
    Path({flag!r}).write_text("held")
    time.sleep(3)
'''


def test_r2_6_another_process_holding_the_design_lock_makes_an_edit_wait(estate):  # noqa: F811
    m, svc, doc, _ = _proposed(estate, model=False)
    flag = estate.root / f"held-{doc['id']}"
    code = _HOLDER.format(paths=list(sys.path), root=str(m.out_root), did=doc["id"],
                          flag=str(flag))
    p = subprocess.Popen([sys.executable, "-c", code], env=dict(os.environ))
    try:
        for _ in range(300):
            if flag.exists():
                break
            time.sleep(0.05)
        assert flag.exists(), "the holder process did not take the lock"
        t0 = time.monotonic()
        m.edit_design(doc["id"], {"columns": doc["design"]["columns"][:-2]}, by="staff",
                      service=svc, rev=doc["rev"])
        waited = time.monotonic() - t0
    finally:
        p.wait(30)
    assert waited >= 1.5, f"the edit did not wait for the other process ({waited:.2f}s)"
