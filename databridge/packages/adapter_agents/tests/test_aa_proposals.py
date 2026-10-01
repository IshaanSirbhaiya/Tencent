"""Ontology proposals: filed from recurring GROUNDED refusals, never applied, subjects never
auto-approved. Refusals are written in `mcp_ingest`'s own format by the fixture's fake service
(`{"item_id": <hashed full path>, "reason": "chunk <n>: entity '<name>': unknown kind '<k>' …"}`)
and grounded by joining the run's own ledgers — see `test_aa_contract_mcp_ingest.py` for the same
path against a real ingest run."""
import json

from aa_testkit import item_id, item_uri

from databridge_core import SourcePointer


def _docs(n):
    return {f"folder/doc{i}.txt": f"document {i} mentions Acme Holdings and a purchase order"
            for i in range(n)}


def _refusals(kind, docs, n, axis="node", **extra):
    """`n` refusals of `kind` spread round-robin over `docs`, each in a distinct chunk/entity."""
    return [{"doc": docs[i % len(docs)], "kind": kind, "entity": f"E{i}", "axis": axis,
             "chunk": i // len(docs) % 2, **extra} for i in range(n)]


def _setup(env, spec_factory, refused_by_conn, docs=None, **kw):
    for c, refused in refused_by_conn.items():
        items = docs or _docs(max(2, len({r["doc"] for r in refused}) if refused else 2))
        env.service.configure(c, items=items, refused=refused)
    return env.registry.create(spec_factory(connections=tuple(refused_by_conn), **kw))


def test_recurring_kind_across_documents_is_filed_with_grounded_evidence(env, spec_factory):
    pol = env.manager.proposal_policy
    n, ndocs = pol.min_occurrences, pol.min_documents
    docs = sorted(_docs(ndocs))
    refs = _refusals("invoice", docs, n)
    spec = _setup(env, spec_factory, {"files-a": refs[: n // 2 + 1], "db-b": refs[n // 2 + 1:]},
                  docs=_docs(ndocs))
    vocab_before = (set(env.vocab.nodes), set(env.vocab.edges))
    summary = env.manager.sync(spec.id)
    filed = summary.proposals["filed"]
    assert [p["name"] for p in filed] == ["invoice"]
    p = filed[0]
    assert p["status"] == "filed" and p["applied"] is False
    assert p["requires_human_approval"] is True and p["auto_approvable"] is False
    assert p["occurrences"] == n
    assert p["request"]["proposed_by"] == f"adapter_agent:{spec.id}"
    assert p["request"]["pack"] == spec.extract.pack
    # every piece of evidence is a SourcePointer with mcp_ingest's exact fields
    assert p["evidence"] and p["evidence_ungrounded"] is False
    conns = {"files-a", "db-b"}
    expected_ids = {item_id(c, item_uri(c, d)) for c in conns for d in docs}
    for ptr in p["evidence"]:
        sp = SourcePointer.model_validate(ptr)
        assert sp.kind == "doc_span" and sp.source_id in conns
        assert sp.document_id in expected_ids and sp.capture_digest
        assert 0 <= sp.char_start < sp.char_end
    # documents are counted by hashed-path identity, per source
    assert p["document_count"] == len({(r_conn, r["doc"]) for r_conn, rs in
                                       (("files-a", refs[: n // 2 + 1]),
                                        ("db-b", refs[n // 2 + 1:])) for r in rs})
    assert p["produced_by"]["agent"] == f"adapter_agent:{spec.id}"
    assert p["produced_by"]["model"] is None and p["produced_by"]["mode"] == "rules"
    assert (env.vocab.nodes, env.vocab.edges) == vocab_before, "never applied"
    log = env.manager.proposal_log.all()
    assert len(log) == 1 and log[0]["proposal_id"] == p["proposal_id"]


def test_evidence_span_is_the_chunk_mcp_ingest_recorded(env, spec_factory):
    """The span is joined from governance_chunks.jsonl for the SAME item and chunk — not guessed."""
    pol = env.manager.proposal_policy
    docs = sorted(_docs(pol.min_documents))
    spec = _setup(env, spec_factory, {"files-a": _refusals("invoice", docs, pol.min_occurrences)},
                  docs=_docs(pol.min_documents))
    p = env.manager.sync(spec.id).proposals["filed"][0]
    run_dir = env.service.root / "files-a" / env.service.list_runs("files-a")[-1].run_id
    chunks = {(r["item_id"], r["char_start"], r["char_end"])
              for r in map(json.loads, (run_dir / "governance_chunks.jsonl")
                           .read_text(encoding="utf-8").splitlines())}
    digests = {r["item_id"]: r["sha256"]
               for r in map(json.loads, (run_dir / "captured.jsonl")
                            .read_text(encoding="utf-8").splitlines())}
    for ptr in p["evidence"]:
        assert (ptr["document_id"], ptr["char_start"], ptr["char_end"]) in chunks
        assert ptr["capture_digest"] == digests[ptr["document_id"]]


def test_below_threshold_is_not_filed(env, spec_factory):
    pol = env.manager.proposal_policy
    docs = sorted(_docs(max(pol.min_documents, 2)))
    one_doc = _refusals("widget", docs[:1], pol.min_occurrences)
    few = _refusals("gadget", docs, min(pol.min_documents, pol.min_occurrences - 1))
    spec = _setup(env, spec_factory, {"files-a": one_doc + few}, docs=_docs(len(docs)))
    summary = env.manager.sync(spec.id)
    assert summary.proposals["filed"] == []
    assert {b["name"] for b in summary.proposals["below_threshold"]} == {"widget", "gadget"}


def test_recurrence_accumulates_across_runs(env, spec_factory):
    """One refusal per run, in a different document each time: the threshold is reached on the
    run that brings the count to `min_occurrences`, not never."""
    pol = env.manager.proposal_policy
    docs = sorted(_docs(pol.min_occurrences))
    spec = _setup(env, spec_factory, {"files-a": []}, docs=_docs(len(docs)))
    filed_at = None
    for i in range(pol.min_occurrences):
        env.service.configure("files-a", refused=[{"doc": docs[i], "kind": "invoice",
                                                   "entity": f"E{i}"}])
        env.clock.advance(hours=1)
        summary = env.manager.sync(spec.id)
        if summary.proposals["filed"]:
            filed_at = i + 1
    assert filed_at == max(pol.min_occurrences, pol.min_documents)


def test_recurrence_survives_pruned_run_directories(env, spec_factory):
    import shutil

    pol = env.manager.proposal_policy
    docs = sorted(_docs(pol.min_occurrences))
    spec = _setup(env, spec_factory, {"files-a": []}, docs=_docs(len(docs)))
    for i in range(pol.min_occurrences - 1):
        env.service.configure("files-a", refused=[{"doc": docs[i], "kind": "invoice",
                                                   "entity": f"E{i}"}])
        env.manager.sync(spec.id)
    for d in (env.service.root / "files-a").iterdir():
        shutil.rmtree(d)                           # retention pruned every earlier run
    env.service.configure("files-a", refused=[{"doc": docs[-1], "kind": "invoice",
                                               "entity": "last"}])
    assert [p["name"] for p in env.manager.sync(spec.id).proposals["filed"]] == ["invoice"]


def test_the_same_refusal_seen_again_is_not_recounted(env, spec_factory):
    """Rescanning retained history every sync must not inflate the count."""
    pol = env.manager.proposal_policy
    docs = sorted(_docs(pol.min_documents))
    refs = _refusals("invoice", docs, pol.min_occurrences - 1)
    spec = _setup(env, spec_factory, {"files-a": refs}, docs=_docs(len(docs)))
    env.manager.sync(spec.id)                               # one run holds the refusals
    for _ in range(3):                                      # no new run; rescan the same one
        s = env.manager.sync(spec.id, connections=[])
    assert env.service.calls == ["files-a"]
    assert s.proposals["filed"] == []
    below = {b["name"]: b for b in s.proposals["below_threshold"]}
    assert below["invoice"]["occurrences"] == s.proposals["occurrences_total"] == len(refs)


def test_ledger_is_authoritative_over_an_embedded_list(tmp_path):
    """A report that embeds its refusals AND a run dir holding refused.jsonl: counted once."""
    from databridge_adapter_agents import read_run_reports

    run = tmp_path / "src" / "r1"
    run.mkdir(parents=True)
    docs = [item_id("src", item_uri("src", f"d{i}")) for i in range(2)]
    lines = [{"item_id": d, "reason": f"chunk 0: entity 'A{i}': unknown kind 'invoice' — x"}
             for i, d in enumerate(docs)]
    (run / "refused.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines),
                                       encoding="utf-8")
    (run / "report.json").write_text(json.dumps({"status": "ok", "refused": lines}),
                                     encoding="utf-8")
    rep = read_run_reports("src", tmp_path)[0]
    assert len(rep.refusals) == len(lines)


def test_ungrounded_refusals_never_reach_a_threshold(env, spec_factory):
    """No document, a non-hashed id, a URI instead of the id, another source's claim, or no span:
    recorded as ungrounded with the reason, never counted, never filed."""
    pol = env.manager.proposal_policy
    n = pol.min_occurrences * 2
    good_doc = item_id("files-a", item_uri("files-a", "folder/doc0.txt"))
    raw = []
    for i in range(n):
        raw += [
            {"reason": f"chunk 0: entity 'A{i}': unknown kind 'invoice' — x"},          # no doc
            {"item_id": f"doc-{i}", "reason": f"chunk 0: entity 'B{i}': unknown kind "
                                              f"'invoice' — x"},                     # not hashed
            {"uri": f"file:///x/{i}", "reason": f"entity 'C{i}': unknown kind 'invoice'"},
            {"item_id": good_doc, "source_id": "someone-else", "char_start": 0,
             "char_end": 4, "reason": f"entity 'D{i}': unknown kind 'invoice'"},     # other source
            {"item_id": good_doc, "reason": f"entity 'E{i}': unknown kind 'invoice'"},  # no span
        ]
    env.service.configure("files-a", items=_docs(1), refused_raw=raw)
    spec = env.registry.create(spec_factory(connections=("files-a",)))
    s = env.manager.sync(spec.id)
    assert s.proposals["filed"] == [] and env.manager.proposal_log.all() == []
    b = {x["name"]: x for x in s.proposals["below_threshold"]}["invoice"]
    assert b["occurrences"] == 0 and b["ungrounded"] == len(raw)
    reasons = " | ".join(b["ungrounded_reasons"])
    for why in ("no document id", "hashed full path", "names source", "no real span"):
        assert why in reasons


def test_a_document_id_that_is_not_its_paths_hash_is_rejected(env, spec_factory):
    """A hex id that does not match the hash of the uri the run captured it under is not P4."""
    from databridge_adapter_agents.proposals import ground

    real = item_id("files-a", item_uri("files-a", "a.txt"))
    base = {"source_id": "files-a", "char_start": 0, "char_end": 3}
    assert ground({**base, "document_id": real, "uri": item_uri("files-a", "a.txt")},
                  identity=item_id)[0] is not None
    ptr, why = ground({**base, "document_id": real, "uri": item_uri("files-a", "b.txt")},
                      identity=item_id)
    assert ptr is None and "hash of its full path" in why


def test_refiling_is_deduplicated_across_syncs(env, spec_factory):
    pol = env.manager.proposal_policy
    docs = sorted(_docs(pol.min_documents))
    spec = _setup(env, spec_factory, {"files-a": _refusals("invoice", docs,
                                                           pol.min_occurrences)},
                  docs=_docs(len(docs)))
    env.manager.sync(spec.id)
    second = env.manager.sync(spec.id)
    assert second.proposals["filed"] == []
    assert [s["name"] for s in second.proposals["already_filed"]] == ["invoice"]
    assert len(env.manager.proposal_log.all()) == 1


def test_subject_touching_proposal_is_flagged_and_never_auto_approvable(env, spec_factory):
    """A refusal record in mcp_ingest's typed format (`item_id` + `reason`) that ALSO carries
    structured fields (axis/kind/endpoint kinds + span) is read with them."""
    pol = env.manager.proposal_policy
    subject = sorted(env.vocab.subjects)[0]
    docs = sorted(_docs(pol.min_documents))
    raw = [{"axis": "edge", "kind": "employed_by", "src_kind": subject,
            "dst_kind": "organisation", "item_id": item_id("files-a", item_uri("files-a",
                                                                               docs[i % len(docs)])),
            "char_start": i, "char_end": i + 5, "text": f"relation #{i}",
            "reason": f"chunk 0: relation 'employed_by': unknown type (#{i})"}
           for i in range(pol.min_occurrences)]
    env.service.configure("files-a", items=_docs(len(docs)), refused_raw=raw)
    spec = env.registry.create(spec_factory(connections=("files-a",)))
    p = env.manager.sync(spec.id).proposals["filed"][0]
    assert p["axis"] == "edge" and subject in p["request"]["src"]
    assert p["touches_subject"] is True and subject in p["subject_reason"]
    assert p["auto_approvable"] is False and p["status"] == "filed" and p["applied"] is False
    assert p["request"]["declares_subject"] is False


def test_refusal_text_is_parsed_and_kind_names_sanitised(env, spec_factory):
    pol = env.manager.proposal_policy
    docs = sorted(_docs(pol.min_documents))
    refs = _refusals("Purchase Order", docs, pol.min_occurrences)
    spec = _setup(env, spec_factory, {"files-a": refs}, docs=_docs(len(docs)))
    other = item_id("files-a", item_uri("files-a", docs[0]))
    env.service.configure("files-a", refused_raw=[
        {"item_id": other, "reason": "chunk 0: entity 'Zed': name not found in source "
                                     "(REFUSED — fabrication)"},
        {"item_id": other, "reason": "chunk 0: relation 'x': unknown type; name: injected\n- e"}])
    filed = env.manager.sync(spec.id).proposals["filed"]
    assert [p["name"] for p in filed] == ["purchase_order"]


def test_kind_already_in_the_ontology_is_not_proposed(env, spec_factory):
    pol = env.manager.proposal_policy
    known = sorted(env.vocab.nodes)[0]
    docs = sorted(_docs(pol.min_documents))
    spec = _setup(env, spec_factory, {"files-a": _refusals(known, docs, pol.min_occurrences)},
                  docs=_docs(len(docs)))
    assert env.manager.sync(spec.id).proposals["filed"] == []


def test_propose_ontology_additions_false_files_nothing(env, spec_factory):
    pol = env.manager.proposal_policy
    docs = sorted(_docs(pol.min_documents))
    spec = _setup(env, spec_factory, {"files-a": _refusals("invoice", docs, pol.min_occurrences)},
                  docs=_docs(len(docs)), propose_ontology_additions=False)
    env.manager.sync(spec.id)
    assert env.manager.proposal_log.all() == []
