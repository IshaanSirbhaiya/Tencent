"""Scenario 2 (+3 helper): graph connectivity audit over the shared e2e graph store (read-only).

    python scripts/e2e/graph_audit.py [label]   -> <e2e>/artefacts/graph_audit[_label].json
"""
from __future__ import annotations

import collections
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_env import e2e_dir  # noqa: E402


def _sources_of(ptr: dict) -> set[str]:
    out = {ptr.get("source_id")} if ptr.get("source_id") else set()
    for d in ptr.get("derived_from") or []:
        out |= _sources_of(d)
    return out


def audit(db: Path) -> dict:
    c = sqlite3.connect(str(db))
    nodes = {r[0]: {"kind": r[1], "label": r[2], "canonical_id": r[3]} for r in c.execute(
        "select id,kind,label,canonical_id from nodes where system_to is null and valid_to is null")}
    edges = [dict(zip(("id", "src", "dst", "kind"), r)) for r in c.execute(
        "select id,src_id,dst_id,kind from edges where system_to is null and valid_to is null")]
    srcs: dict[str, set] = collections.defaultdict(set)
    for eid, pj in c.execute("select entity_id, source_pointer_json from sources"):
        srcs[eid] |= _sources_of(json.loads(pj))
    # an entity is also touched by a source when an edge from that source ends on it
    touched: dict[str, set] = collections.defaultdict(set)
    for nid in nodes:
        touched[nid] |= srcs.get(nid, set())
    for e in edges:
        for end in (e["src"], e["dst"]):
            touched[end] |= srcs.get(e["id"], set())
    adj = collections.defaultdict(set)
    for e in edges:
        if e["src"] in nodes and e["dst"] in nodes:
            adj[e["src"]].add(e["dst"]); adj[e["dst"]].add(e["src"])
    seen, comps = set(), []
    for n in nodes:
        if n in seen:
            continue
        stack, comp = [n], []
        seen.add(n)
        while stack:
            x = stack.pop(); comp.append(x)
            for y in adj[x]:
                if y not in seen:
                    seen.add(y); stack.append(y)
        comps.append(comp)
    comps.sort(key=len, reverse=True)
    largest = comps[0] if comps else []
    own = {n: srcs.get(n, set()) for n in nodes}
    cross_own = sorted(n for n, s in own.items() if len(s) >= 2)
    cross_touch = sorted(n for n, s in touched.items() if n in nodes and len(s) >= 2)
    comp_of = {n: i for i, comp in enumerate(comps) for n in comp}
    src_by_comp = collections.defaultdict(set)
    for n in nodes:
        src_by_comp[comp_of[n]] |= touched[n]
    multi_src_comps = [i for i, s in src_by_comp.items() if len(s) >= 2]
    probes = {}
    for term in ("pellucid", "rowan", "achterberg", "inv-2026-0041", "inv-2026-0063", "msa-2026-014",
                 "tamsinbay", "corriedale", "selin"):
        hits = [n for n, v in nodes.items() if term in (n + " " + v["label"]).lower()]
        probes[term] = [{"id": n, "kind": nodes[n]["kind"], "label": nodes[n]["label"],
                         "sources": sorted(own[n]), "component": comp_of[n],
                         "component_size": len(comps[comp_of[n]]),
                         "degree": len(adj[n])} for n in hits]
    dangling = [e["id"] for e in edges if e["src"] not in nodes or e["dst"] not in nodes]
    return {
        "db": str(db), "nodes": len(nodes), "edges": len(edges),
        "nodes_by_kind": dict(collections.Counter(v["kind"] for v in nodes.values())),
        "edges_by_kind": dict(collections.Counter(e["kind"] for e in edges)),
        "nodes_by_source": dict(collections.Counter(",".join(sorted(s)) or "(none)"
                                                    for s in own.values())),
        "components": len(comps), "component_sizes_top10": [len(x) for x in comps[:10]],
        "singletons": sum(1 for x in comps if len(x) == 1),
        "largest_component_share": round(len(largest) / max(1, len(nodes)), 3),
        "largest_component_sources": sorted(src_by_comp[0]) if comps else [],
        "components_spanning_both_sources": len(multi_src_comps),
        "cross_source_entities_own_pointers": [
            {"id": n, "kind": nodes[n]["kind"], "label": nodes[n]["label"]} for n in cross_own],
        "cross_source_entities_touched": [
            {"id": n, "kind": nodes[n]["kind"], "label": nodes[n]["label"],
             "sources": sorted(touched[n])} for n in cross_touch],
        "probes": probes, "dangling_edges": dangling[:20], "dangling_count": len(dangling),
    }


if __name__ == "__main__":
    label = sys.argv[1] if len(sys.argv) > 1 else ""
    E = e2e_dir()
    out = audit(E / "graph" / "workspace.db")
    p = E / "artefacts" / f"graph_audit{'_' + label if label else ''}.json"
    p.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "probes"}, indent=1)[:6000])
    for t, hits in out["probes"].items():
        print("PROBE", t, [(h["id"], h["sources"], h["component"], h["degree"]) for h in hits])
