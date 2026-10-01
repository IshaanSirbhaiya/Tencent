"""A kind the owner removed on /ontology (deprecated, never deleted) is no longer offered to the
model, and model output using it is refused — "the system only maps to" the live ontology.

Kinds are picked from the bound vocabulary, never named, and each case has a control: the same kind
is offered again once reactivated, so the test cannot pass by refusing everything.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from databridge_convert.ir.models import Block, DocumentRecord
from databridge_core import SnapshotIdentity
from databridge_graph.extractor import extract_graph, extraction_vocabulary, gold_extraction_handler
from databridge_graph.packs import active_vocabulary, deployment
from databridge_harness import FakeBackend, Harness

T0 = dt.datetime(2026, 3, 1, tzinfo=dt.timezone.utc)
PACK = "workspace"


def _write_state(path, records):
    path.write_text(json.dumps({"deprecated_kinds": records}), encoding="utf-8")


@pytest.fixture()
def state(tmp_path, monkeypatch):
    p = tmp_path / "ontology_state.json"
    monkeypatch.setenv("WALNUT_ONTOLOGY_STATE_PATH", str(p))
    return p


def _pick_node_kind(vocab) -> str:
    kinds = sorted(k for k in vocab.node_kinds if k not in vocab.subject_kinds)
    assert kinds, "the bound pack declares no non-subject node kind"
    return kinds[0]


def test_deprecated_node_kind_is_withheld_then_offered_again_on_reactivation(state):
    with deployment(PACK):
        vocab = active_vocabulary()
        kind = _pick_node_kind(vocab)
        assert kind in extraction_vocabulary().node_kinds          # control: offered before

        _write_state(state, [{"pack": PACK, "axis": "node", "kind": kind, "by": "owner"}])
        after = extraction_vocabulary().node_kinds
        assert kind not in after
        assert set(after) == set(vocab.node_kinds) - {kind}       # nothing else was removed

        _write_state(state, [{"pack": PACK, "axis": "node", "kind": kind, "by": "owner"},
                             {"pack": PACK, "axis": "node", "kind": kind, "reactivated": True}])
        assert kind in extraction_vocabulary().node_kinds          # last record wins


def test_deprecation_in_an_unbound_pack_changes_nothing(state):
    with deployment(PACK):
        kind = _pick_node_kind(active_vocabulary())
        _write_state(state, [{"pack": "some_other_pack", "axis": "node", "kind": kind}])
        assert kind in extraction_vocabulary().node_kinds


def test_model_output_using_a_deprecated_kind_is_refused(state):
    with deployment(PACK):
        vocab = active_vocabulary()
        kind, keep = sorted(k for k in vocab.node_kinds if k not in vocab.subject_kinds)[:2]
        _write_state(state, [{"pack": PACK, "axis": "node", "kind": kind}])
        text = "Alpha Widget met Beta Gadget."
        ents = [{"name": "Alpha Widget", "kind": kind, "char_start": 0, "char_end": 12},
                {"name": "Beta Gadget", "kind": keep, "char_start": 17, "char_end": 28}]
        harness = Harness(FakeBackend(gold_extraction_handler(ents, [])), profile="cloud")
        snap = SnapshotIdentity(source_id="d1", as_of=T0, content_hash="h", schema_hash="s")
        doc = DocumentRecord(document_id="d1", sha256="x", snapshot=snap,
                             blocks=[Block(kind="Paragraph", text=text)])
        res = extract_graph(doc, harness)
        got = {str(n.kind.value if hasattr(n.kind, "value") else n.kind) for n in res.entities}
        assert keep in got                                         # control: allowed kind lands
        assert kind not in got
        assert any(kind in r for r in res.refused)


# --- review R-017 finding 8: the state fails CLOSED, and writers are serialised ---------------
def test_an_unreadable_state_file_fails_closed_not_open(state):
    """A corrupt state used to read as "nothing deprecated", silently offering every removed kind
    again. Extraction must refuse to run against a vocabulary whose removals it cannot establish."""
    from databridge_graph.ontology_state import OntologyStateError, currently_deprecated

    with deployment(PACK):
        kind = _pick_node_kind(active_vocabulary())
        _write_state(state, [{"pack": PACK, "axis": "node", "kind": kind}])
        assert kind not in extraction_vocabulary().node_kinds         # control: honoured
        state.write_bytes(state.read_bytes()[:-7])                     # truncated mid-record
        with pytest.raises(OntologyStateError):
            currently_deprecated([PACK])
        with pytest.raises(OntologyStateError):
            extraction_vocabulary()


def test_an_absent_state_file_is_empty_state(state):
    from databridge_graph.ontology_state import currently_deprecated

    assert not state.exists()
    assert currently_deprecated([PACK]) == (frozenset(), frozenset())


_WRITER = r"""
import sys
from databridge_graph.ontology_state import file_lock, read_state, state_lock_path, write_state
tag, n = sys.argv[1], int(sys.argv[2])
for i in range(n):
    with file_lock(state_lock_path()):
        s = read_state()
        s.setdefault("deprecated_kinds", []).append({"pack": "p", "axis": "node", "kind": f"{tag}_{i}"})
        write_state(s)
"""


def test_concurrent_writer_processes_lose_no_records(state, tmp_path):
    """Read-modify-write from several PROCESSES under `file_lock`: every record survives, and the
    file is valid JSON throughout (atomic replace). Counts are derived from the parameters."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    import databridge_graph
    from databridge_graph.ontology_state import read_state

    procs, per = 4, 15
    script = tmp_path / "writer.py"
    script.write_text(_WRITER, encoding="utf-8")
    env = {**os.environ, "WALNUT_ONTOLOGY_STATE_PATH": str(state),
           "PYTHONPATH": os.pathsep.join([str(Path(databridge_graph.__file__).parents[1]),
                                          os.environ.get("PYTHONPATH", "")])}
    running = [subprocess.Popen([sys.executable, str(script), f"w{k}", str(per)], env=env)
               for k in range(procs)]
    assert all(p.wait(timeout=120) == 0 for p in running)
    kinds = [r["kind"] for r in read_state()["deprecated_kinds"]]
    assert len(kinds) == procs * per
    assert len(set(kinds)) == procs * per


# --- review R-019 (Codex batch 2, ontology-r2) ------------------------------------------------
@pytest.mark.parametrize("bad", [
    {"pack": PACK, "axis": "node"},                                    # no kind
    {"pack": PACK, "axis": "nodes", "kind": "x"},                      # unknown axis
    {"pack": PACK, "axis": "node", "kind": "x", "reactivated": "no"},  # string, not a boolean
    "not an object",
])
def test_a_malformed_deprecation_record_fails_closed_instead_of_being_skipped(state, bad):
    """Finding 6: a malformed record was SKIPPED — and if it was the kind's latest record, the
    skip silently reactivated the kind for extraction. Every record is now validated."""
    from databridge_graph.ontology_state import OntologyStateError, currently_deprecated

    with deployment(PACK):
        kind = _pick_node_kind(active_vocabulary())
        _write_state(state, [{"pack": PACK, "axis": "node", "kind": kind}, bad])
        with pytest.raises(OntologyStateError):
            currently_deprecated([PACK])
        with pytest.raises(OntologyStateError):
            extraction_vocabulary()


def test_a_string_boolean_cannot_turn_auto_approve_on(state):
    """Finding 6: ``bool("false")`` is True, so ``"enabled": "false"`` used to ENABLE it."""
    from databridge_graph.ontology_state import OntologyStateError, read_state

    state.write_text(json.dumps({"auto_approve": {"enabled": "false"}}), encoding="utf-8")
    with pytest.raises(OntologyStateError):
        read_state()
    state.write_text(json.dumps({"auto_approve": {"enabled": False}}), encoding="utf-8")
    assert read_state()["auto_approve"]["enabled"] is False           # control


def test_atomic_replacement_carries_the_targets_permission_bits(tmp_path, monkeypatch):
    """Finding 8: `mkstemp` makes 0600; the temp file must get the target's mode before the
    replace. Asserted on the chmod call (Windows `chmod` only carries the read-only flag, so the
    resulting mode alone would prove nothing there)."""
    import os
    import stat

    from databridge_graph import ontology_state

    target = tmp_path / "ontology.yaml"
    target.write_text("a: 1\n", encoding="utf-8")
    want = stat.S_IMODE(os.stat(target).st_mode)
    calls: list[tuple[str, int]] = []
    real = os.chmod
    monkeypatch.setattr(ontology_state.os, "chmod",
                        lambda p, m, *a, **k: (calls.append((str(p), m)), real(p, m, *a, **k)))
    ontology_state.atomic_write_text(target, "a: 2\n")
    assert target.read_text(encoding="utf-8") == "a: 2\n"
    assert [m for p, m in calls if p != str(target)] == [want]
    assert stat.S_IMODE(os.stat(target).st_mode) == want
