"""Router scaling, one-copy vector memory, search_ram honesty, the offline label, depth levels, CLI."""
from __future__ import annotations

import json
import sqlite3
import sys
import tracemalloc

import numpy as np
import pytest

from retrieval_testkit import prose, write_capture


# --- router ------------------------------------------------------------------------------------
def test_centroid_count_scales_within_floor_and_ceiling():
    from databridge_retrieval.vectors import (
        CENTROID_DIVISOR, CENTROID_MAX, CENTROID_MIN, centroid_count)

    ns = [0, 1, CENTROID_MIN - 1, CENTROID_MIN, 100, CENTROID_DIVISOR * CENTROID_MIN,
          CENTROID_DIVISOR * (CENTROID_MIN + 5), CENTROID_DIVISOR * CENTROID_MAX,
          CENTROID_DIVISOR * CENTROID_MAX * 10]
    counts = [centroid_count(n) for n in ns]
    assert counts == sorted(counts), "monotonic in store size"
    assert all(c <= n for c, n in zip(counts, ns)), "never more centroids than vectors"
    assert centroid_count(100) == CENTROID_MIN                       # floor
    assert centroid_count(CENTROID_DIVISOR * CENTROID_MAX * 10) == CENTROID_MAX   # ceiling
    for x in range(CENTROID_MIN, CENTROID_MAX + 1):                  # scales in between
        assert centroid_count(CENTROID_DIVISOR * x) == x
    assert centroid_count(CENTROID_MIN - 1) == CENTROID_MIN - 1


def test_published_store_carries_scaled_centroids_and_honest_ram(dirs, fake_harness, screen):
    from databridge_retrieval import BuildSession, stats
    from databridge_retrieval.vectors import centroid_count

    cap, pas = dirs
    h, _ = fake_harness()
    with BuildSession("r", harness=h, screen=screen, scope="Plant operating notes") as b:
        for i in range(6):
            b.index_capture(write_capture(cap, prose(4000, salt=f"r{i}"),
                                          {"source": "r", "uri": f"file:///r/{i}.md"}))
    (e,) = stats()
    assert e["chunks"] > 0 and e["centroids"] == centroid_count(e["chunks"])
    assert e["scope"] == "Plant operating notes"
    assert e["search_ram_bytes"] == e["embeddings"] * e["embed_dim"] * 4
    assert e["search_ram_mb"] > 0                  # tiny, but never published as 0
    assert e["throughput"]["texts_embedded"] == e["chunks"]
    assert e["throughput"]["texts_per_second"] > 0


def test_search_ram_is_null_when_unknown_never_zero():
    from databridge_retrieval.vectors import search_ram_bytes, search_ram_mb

    assert search_ram_mb(0, 384) is None and search_ram_mb(10, None) is None
    assert search_ram_bytes(None, 384) is None
    assert search_ram_mb(1, 1) > 0


def test_router_prunes_to_top_stores_and_reports_the_rest(dirs, fake_harness, screen):
    from databridge_retrieval import BuildSession, Retriever

    cap, pas = dirs
    h, _ = fake_harness()
    topics = {"alpha": "Invoices, remittance advice and supplier ledger balances. ",
              "beta": "Forklift safety, warehouse racking and pallet inspections. ",
              "gamma": "Recipes for sourdough, rye starters and oven temperatures. "}
    for src, sentence in topics.items():
        with BuildSession(src, harness=h, screen=screen) as b:
            b.index_capture(write_capture(cap, sentence * 40, {"source": src,
                                                               "uri": f"file:///{src}"}))
    res = Retriever(harness=h, route_top=1).search(
        "forklift racking pallet inspections", k=3, mode="vector")
    searched = {s["source_id"] for s in res.coverage["searched"]}
    skipped = {s["source_id"] for s in res.coverage["skipped"]}
    assert searched == {"beta"}
    assert skipped == set(topics) - {"beta"}
    assert all("routed out" in s["reason"] for s in res.coverage["skipped"])
    assert res and {p.source_id for p in res} == {"beta"}
    # with room for every store, nothing is routed out
    full = Retriever(harness=h, route_top=6).search("forklift", k=3, mode="vector")
    assert not full.coverage["skipped"]


def test_structural_discovery_ignores_non_store_sqlite(dirs, fake_harness, screen):
    from databridge_retrieval import BuildSession, stats

    cap, pas = dirs
    h, _ = fake_harness()
    with BuildSession("s", harness=h, screen=screen) as b:
        b.index_capture(write_capture(cap, prose(500), {"source": "s", "uri": "file:///s"}))
    facts = sqlite3.connect(str(pas / "s__facts.sqlite"))
    facts.execute("CREATE TABLE invoices(id, amount)")
    facts.commit()
    facts.close()
    assert [e["source_id"] for e in stats()] == ["s"]


# --- memory: one copy of the vectors ------------------------------------------------------------
def _embedding_db(n: int, dim: int) -> sqlite3.Connection:
    from databridge_retrieval import store as st

    conn = sqlite3.connect(":memory:")
    st.init_schema(conn)
    rng = np.random.default_rng(1)
    conn.executemany("INSERT INTO embeddings VALUES (?,?,?,?)",
                     ((f"c{i:07d}", rng.standard_normal(dim).astype(np.float32).tobytes(), "m",
                       dim) for i in range(n)))
    conn.commit()
    return conn


def _naive_triple_copy(conn):
    rows = conn.execute("SELECT chunk_id, vector FROM embeddings WHERE model='m'").fetchall()
    blobs = [r[1] for r in rows]
    joined = b"".join(blobs)
    m = np.frombuffer(joined, dtype=np.float32).reshape(len(blobs), -1).copy()
    m = m / np.linalg.norm(m, axis=1, keepdims=True)
    return [r[0] for r in rows], m


def test_scoring_holds_one_copy_of_the_vectors():
    from databridge_retrieval.vectors import load_matrix, score

    n, dim = 20000, 256
    conn = _embedding_db(n, dim)
    matrix_bytes = n * dim * 4

    tracemalloc.start()
    ids, m = load_matrix(conn, "m")
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    ids_bytes = sys.getsizeof(ids) + sum(sys.getsizeof(i) for i in ids)
    assert m.shape == (n, dim) and m.dtype == np.float32 and len(ids) == n
    assert np.allclose(np.linalg.norm(m, axis=1), 1.0, atol=1e-5)       # normalised in place
    bound = matrix_bytes * 1.1 + ids_bytes + 2**20
    assert peak <= bound, f"peak {peak} > one-copy bound {bound}"

    # the bound discriminates: the list-of-blobs + join + copy pattern blows through it
    tracemalloc.start()
    _naive_triple_copy(conn)
    _, naive_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert naive_peak > bound

    # block streaming gives the same scores as one big product, without an n x n temporary
    q = m[123].copy()
    tracemalloc.start()
    s = score(m, q, block=1000)
    _, score_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert np.allclose(s, m @ q, atol=1e-5) and int(np.argmax(s)) == 123
    assert score_peak < matrix_bytes / 10
    conn.close()


# --- the offline label --------------------------------------------------------------------------
def test_offline_embedding_is_labelled_in_results_and_stats(dirs, monkeypatch, screen):
    from databridge_harness import OFFLINE_LABEL, Harness, LiteLLMBackend
    from databridge_retrieval import BuildSession, Retriever, stats

    cap, pas = dirs
    for var in ("OPENAI_API_KEY", "AZURE_API_KEY", "AZURE_OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    h = Harness(LiteLLMBackend(), profile="cloud", cache=None)   # no key: never reaches LiteLLM
    with BuildSession("off", harness=h, screen=screen) as b:
        b.index_capture(write_capture(cap, prose(2000), {"source": "off", "uri": "file:///o"}))
    res = Retriever(harness=h).search("widget press maintenance", k=3)
    assert res, "offline search still answers"
    assert res.offline_embedding is True and res.embedding_label == OFFLINE_LABEL
    assert "offline" in res.describe()["embedding_label"]
    (e,) = stats()
    assert e["offline_embedding"] is True and e["embedding_label"] == OFFLINE_LABEL


# --- depth levels -------------------------------------------------------------------------------
def _depth_backend_responses(facets):
    def respond(model, messages, guided_json):
        text = " ".join(m["content"] for m in messages)
        if "Split the user's question" in text:
            return json.dumps({"facets": facets})
        if "Judge which numbered extracts" in text:
            return json.dumps({"keep": [{"index": 1, "relevance": 0.9},
                                        {"index": 0, "relevance": 0.4}]})
        return "{}"
    return respond


def test_facet_split_gives_every_facet_evidence(dirs, fake_harness, screen):
    from databridge_retrieval import BuildSession, Retriever

    cap, pas = dirs
    facets = ["payment terms net thirty", "office relocation date"]
    h, _ = fake_harness(responses=_depth_backend_responses(facets))
    docs = {"terms": "Payment terms are net thirty days from invoice for every order. " * 3,
            "move": "The office relocation date is fixed for the spring bank holiday. " * 3}
    for i in range(6):
        docs[f"noise{i}"] = ("Payment terms payment terms payment terms discussed "
                             f"generally in meeting {i}. ") * 3
    with BuildSession("f", harness=h, screen=screen) as b:
        ids = {k: b.index_capture(write_capture(cap, v, {"source": "f", "uri": f"file:///{k}"})
                                  ).doc_id for k, v in docs.items()}
    r = Retriever(harness=h)
    query = "What are the payment terms, and when is the office relocation?"
    deep = r.deep_search(query, depth="facets", k=3)
    per_facet_best = [r.search(f, k=1)[0].doc_id for f in facets]
    assert set(per_facet_best) <= {p.doc_id for p in deep}
    assert any("model" in n for n in deep.notices)

    reranked = r.deep_search(query, depth="rerank", k=3)
    assert [p.score for p in reranked] == [0.9, 0.4]
    assert any("dropped" in n for n in reranked.notices)


def test_facet_split_falls_back_to_rules_when_the_model_fails(dirs, fake_harness, screen):
    from databridge_retrieval import BuildSession, Retriever, rule_facets

    cap, pas = dirs
    h, _ = fake_harness(responses="not json")
    with BuildSession("g", harness=h, screen=screen) as b:
        b.index_capture(write_capture(cap, prose(800), {"source": "g", "uri": "file:///g"}))
    q = "widget press maintenance and operator training on the jig"
    res = Retriever(harness=h).deep_search(q, depth="facets", k=4)
    assert any(n.startswith("facets (rules") for n in res.notices)
    assert len(rule_facets(q)) == 2


# --- CLI ----------------------------------------------------------------------------------------
def test_cli_index_search_stats(dirs, fake_harness, capsys):
    from databridge_retrieval import configure
    from databridge_retrieval.__main__ import main

    cap, pas = dirs
    h, _ = fake_harness()
    configure(harness=h)
    for i in range(2):
        write_capture(cap, prose(1500, salt=f"c{i}"), {"source": "cli", "uri": f"file:///{i}"})
    write_capture(cap, prose(600, salt="other"), {"source": "other", "uri": "file:///x"})
    assert main(["index", "--source", "cli", "--all"]) == 0
    out = capsys.readouterr().out
    assert out.count("indexed") == 2
    assert main(["search", "widget inspection", "--json", "--k", "3"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["results"] and payload["count"] == len(payload["results"]) <= 3
    assert all(r["source_id"] == "cli" for r in payload["results"])
    assert main(["stats", "--json"]) == 0
    (entry,) = json.loads(capsys.readouterr().out)
    assert entry["documents"] == 2
    assert main(["index", "--source", "nothing-here", "--all"]) == 2
