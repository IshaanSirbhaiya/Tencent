"""Run the Internal Brain leak eval end to end and write RESULTS.md + results.json.

    cd databridge
    .venv\\Scripts\\python.exe -m evals.internal_brain.run                 # full run (~2-10 min, machine-dependent)
    .venv\\Scripts\\python.exe -m evals.internal_brain.run --limit 60      # quick subset
    .venv\\Scripts\\python.exe -m evals.internal_brain.run --workers 1     # one world-A build

Orchestration: the probe set is generated from ``world.json`` up front. World A (the real world) is
built in ``--workers`` shard processes (each builds the E2E world ONCE and runs its share of the
probes in both model modes); the P2 worlds B (one per probing persona, restricted docs removed) and
a world-A control rebuild run in parallel processes. The parent merges everything and writes the
report. ``--worker-spec`` is the internal entry point of a shard.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import HERE, ROOT, WORLD
from .oracle import Oracle, filter_world, load_world, write_world
from .probes import Probe, generate, p2_sample

MODES = ("fake-capture", "extractive")
P2_PERSONAS = ("jdoe", "ken.ext")


# ============================================================================================
# worker
# ============================================================================================
def _build_env(world_path: Path, tmp: Path):
    from e2e_world import E2E
    return E2E(tmp, world_path=world_path)


def run_worker(spec_path: Path) -> int:
    from . import capture as C
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    world_path = Path(spec["world"])
    world = load_world(world_path)
    oracle = Oracle(world)
    probes = [Probe(**p) for p in spec["probes"]]
    keep = set(spec.get("keep_body", []))
    tmp = Path(tempfile.mkdtemp(prefix=f"ibeval-{spec['name']}-"))
    t0 = time.perf_counter()
    env = _build_env(world_path, tmp)
    build_s = time.perf_counter() - t0
    cap = C.Capture()
    C.instrument(env, cap)
    markers = C.injection_markers(oracle)
    records: list[dict] = []
    t1 = time.perf_counter()
    for mode in spec["modes"]:
        C.set_mode(env, cap, mode)
        for p in probes:
            records.append(_probe_with_retry(env, oracle, cap, p, mode, markers, p.id in keep))
    run_s = time.perf_counter() - t1
    canary: dict = {}
    if spec.get("canary"):
        canary = _canaries(env, oracle, cap, markers, [Probe(**p) for p in spec["canary"]["leaky"]],
                           [Probe(**p) for p in spec["canary"]["deny_all"]])
    docs = env.index.doc_ids()
    out = {"name": spec["name"], "world": str(world_path), "build_s": round(build_s, 1),
           "run_s": round(run_s, 1), "n_records": len(records),
           "indexed_docs": len(docs),
           "indexed_by_platform": {pl: sum(1 for d in docs if d.startswith(pl + ":"))
                                   for pl in ("confluence", "jira", "slack", "drive")},
           "connectors": sorted(env.connectors), "records": records, "canary": canary}
    Path(spec["out"]).write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


def _probe_with_retry(env, oracle, cap, probe, mode, markers, keep_body, retries=2) -> dict:
    """A Gate-2 ``timeout`` is a fail-closed DENY by design, but it is an availability event of the
    test host (all shards share one machine), not a property of the content. Such a request is
    re-asked up to ``retries`` times; the record keeps every attempt's timeout reasons and the leaks
    and violations of EVERY attempt (nothing found on an earlier attempt is dropped)."""
    from . import capture as C
    rec = C.run_probe(env, oracle, cap, probe, mode, markers, keep_body=keep_body)
    earlier: list[dict] = []
    while "timeout" in (rec.get("gate2_deny_reasons") or {}) and len(earlier) < retries:
        earlier.append(rec)
        rec = C.run_probe(env, oracle, cap, probe, mode, markers, keep_body=keep_body)
    if earlier:
        rec["timeout_attempts"] = [{"req_id": r.get("req_id"), "gate2_deny_reasons": r.get("gate2_deny_reasons")}
                                   for r in earlier]
        for r in earlier:
            rec["leaks"] = r.get("leaks", []) + rec.get("leaks", [])
            rec["violations"] = r.get("violations", []) + rec.get("violations", [])
    return rec


def _canaries(env, oracle, cap, markers, leaky: list[Probe], deny_all: list[Probe]) -> dict:
    """The eval's own canaries: with Gate 1 + Gate 2 switched OFF the detectors MUST report leaks;
    with a deny-all Gate 1 the utility MUST collapse. Either failing invalidates the run."""
    from . import capture as C
    res: dict = {}
    C.set_mode(env, cap, "fake-capture")
    undo = C.break_access(env)
    try:
        recs = [C.run_probe(env, oracle, cap, p, "fake-capture", markers) for p in leaky]
    finally:
        undo()
    res["leaky"] = {"probes": len(recs), "probes_with_leaks": sum(1 for r in recs if r["leaks"]),
                    "where": sorted({lk["where"] for r in recs for lk in r["leaks"]})}
    C.set_mode(env, cap, "extractive")
    undo = C.deny_all(env)
    try:
        recs = [C.run_probe(env, oracle, cap, p, "extractive", markers) for p in deny_all]
    finally:
        undo()
    util = [u for r in recs for u in r.get("utility", {}).values()]
    res["deny_all"] = {"probes": len(recs),
                       "released_recall": (sum(u["released"] for u in util) / len(util)) if util else None,
                       "leaks": sum(len(r["leaks"]) for r in recs)}
    return res


# ============================================================================================
# parent
# ============================================================================================
def _spec(out_dir: Path, name: str, world: Path, probes: list[Probe], modes, keep=(), canary=None) -> Path:
    spec = {"name": name, "world": str(world), "probes": [p.as_dict() for p in probes],
            "modes": list(modes), "keep_body": sorted(keep), "out": str(out_dir / f"{name}.out.json")}
    if canary:
        spec["canary"] = canary
    path = out_dir / f"{name}.spec.json"
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return path


def _subset(probes: list[Probe], limit: int) -> list[Probe]:
    if not limit or limit >= len(probes):
        return probes
    step = len(probes) / limit
    return [probes[int(i * step)] for i in range(limit)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m evals.internal_brain.run")
    ap.add_argument("--worker-spec", type=Path)
    ap.add_argument("--workers", type=int, default=3, help="world-A shard processes")
    ap.add_argument("--limit", type=int, default=0, help="evenly spaced subset of the probes")
    ap.add_argument("--p2-n", type=int, default=100, help="P2 queries per persona")
    ap.add_argument("--no-p2", action="store_true")
    ap.add_argument("--out", type=Path, default=HERE)
    ap.add_argument("--work", type=Path, default=None, help="scratch dir (specs, shard outputs, worlds)")
    args = ap.parse_args(argv)
    if args.worker_spec:
        return run_worker(args.worker_spec)

    t_start = time.perf_counter()
    world = load_world()
    oracle = Oracle(world)
    all_probes = generate(world, oracle)
    probes = _subset(all_probes, args.limit)
    work = args.work or Path(tempfile.mkdtemp(prefix="ibeval-"))
    work.mkdir(parents=True, exist_ok=True)

    # P2 sample (must be part of what world A runs, so its bodies exist on both sides)
    p2: dict[str, list[Probe]] = {}
    if not args.no_p2:
        for persona in P2_PERSONAS:
            n = args.p2_n if not args.limit else min(args.p2_n, 20)
            p2[persona] = p2_sample(all_probes, persona, n)
    ids = {p.id for p in probes}
    for persona, sample in p2.items():
        probes += [p for p in sample if p.id not in ids]
        ids |= {p.id for p in sample}
    keep = {p.id for s in p2.values() for p in s}

    # canary probes (deterministic picks)
    deny = [p for p in all_probes if p.polarity == "deny" and p.type in ("direct", "paraphrase")]
    leaky = _subset(deny, 20)
    deny_all = _subset([p for p in all_probes if p.polarity == "allow"], 30)
    canary = {"leaky": [p.as_dict() for p in leaky], "deny_all": [p.as_dict() for p in deny_all]}

    specs: list[Path] = []
    shards = [probes[i::max(1, args.workers)] for i in range(max(1, args.workers))]
    for i, shard in enumerate(shards):
        specs.append(_spec(work, f"A{i}", WORLD, shard, MODES,
                           keep=[p.id for p in shard if p.id in keep],
                           canary=canary if i == 0 else None))
    p2_meta: dict = {}
    for persona, sample in p2.items():
        drop = oracle.unreadable(persona)
        wb, rep = filter_world(world, drop)
        path = write_world(wb, work / f"world_B_{persona}.json")
        p2_meta[persona] = {"filter": rep, "queries": len(sample)}
        specs.append(_spec(work, f"B_{persona}", path, sample, MODES, keep=[p.id for p in sample]))
    if p2:
        control = [p for s in p2.values() for p in s]
        specs.append(_spec(work, "A_control", WORLD, control, MODES, keep=[p.id for p in control]))

    print(f"[ibeval] {len(probes)} probes ({len(all_probes)} generated), {len(specs)} shard processes; "
          f"work dir {work}", flush=True)
    procs = []
    env = dict(os.environ, PYTHONIOENCODING="utf-8", IB_FORCE_OFFLINE="1")
    for sp in specs:
        log = open(sp.with_suffix(".log"), "w", encoding="utf-8")
        procs.append((sp, log, subprocess.Popen([sys.executable, "-m", "evals.internal_brain.run",
                                                 "--worker-spec", str(sp)], cwd=str(ROOT),
                                                stdout=log, stderr=subprocess.STDOUT, env=env)))
    outs = {}
    failed = []
    for sp, log, pr in procs:
        rc = pr.wait()
        log.close()
        out = Path(json.loads(sp.read_text(encoding="utf-8"))["out"])
        if rc != 0 or not out.exists():
            failed.append({"shard": sp.stem, "rc": rc,
                           "log_tail": sp.with_suffix(".log").read_text(encoding="utf-8")[-3000:]})
            continue
        o = json.loads(out.read_text(encoding="utf-8"))
        outs[o["name"]] = o
        print(f"[ibeval] shard {o['name']}: build {o['build_s']}s, run {o['run_s']}s, "
              f"{o['n_records']} records", flush=True)
    if failed:
        for f in failed:
            print(f"[ibeval] SHARD FAILED {f['shard']} rc={f['rc']}\n{f['log_tail']}", file=sys.stderr)
    from .report import build, write
    results = build(world=world, oracle=oracle, probes=probes, all_probes=all_probes, outs=outs,
                    p2=p2, p2_meta=p2_meta, failed=failed, runtime_s=time.perf_counter() - t_start,
                    args=vars(args) | {"out": str(args.out), "work": str(work)})
    write(results, args.out)
    h = results["headline"]
    print(f"[ibeval] leaks={h['leaks']} violations={h['violations']} p2_mismatches={h['p2_mismatches']} "
          f"N={h['n_requests']} upper95={h['leak_rate_upper95']:.4f} utility={h['utility_recall']} "
          f"runtime={h['runtime_s']}s -> {args.out / 'RESULTS.md'}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    rc = main()
    if "--worker-spec" in sys.argv:
        # A shard's output is fully written before this point. The eval drives the mock platforms
        # in-process through Starlette's TestClient, which ignores per-call timeouts, so a Gate-2
        # check abandoned at its deadline can leave a pool thread parked forever and block
        # interpreter shutdown. Exit hard so the parent is not left waiting on a finished shard.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(rc)
    raise SystemExit(rc)
