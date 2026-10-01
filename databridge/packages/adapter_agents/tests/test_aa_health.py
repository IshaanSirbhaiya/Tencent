"""health: freshness from report timestamps, counts, exclusions by rule, never-synced is stale."""
import datetime as dt
import json

from databridge_adapter_agents import normalize_report, read_run_reports


def _setup(env, spec_factory, **kw):
    for c in ("files-a", "db-b"):
        env.service.configure(c, items={"d1": f"{c} one", "d2": f"{c} two"})
    return env.registry.create(spec_factory(**kw))


def test_freshness_breach_is_flagged_from_report_timestamps(env, spec_factory):
    spec = _setup(env, spec_factory)
    env.manager.sync(spec.id)
    max_age = spec.freshness.max_age_hours
    report = read_run_reports("files-a", env.service.root)[-1]

    env.clock.t = report.ended + dt.timedelta(hours=max_age - 1)
    h = env.manager.health(spec.id)
    assert h["overall"] == "healthy"
    assert all(not c["stale"] for c in h["connections"])

    env.clock.t = report.ended + dt.timedelta(hours=max_age + 1)
    h = env.manager.health(spec.id)
    assert h["overall"] == "stale"
    for c in h["connections"]:
        assert c["stale"] and c["age_hours"] > max_age
    assert any("stale" in i and f"target {max_age}h" in i for i in h["issues"])


def test_freshness_is_read_from_the_report_not_the_wall_clock_of_the_sync(env, spec_factory):
    """Rewrite one report's `ended` into the past: that connection alone goes stale."""
    spec = _setup(env, spec_factory)
    env.manager.sync(spec.id)
    rep = read_run_reports("db-b", env.service.root)[-1]
    path = env.service.root / "db-b" / rep.run_id / "report.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["finished_at"] = (env.clock.t - dt.timedelta(hours=spec.freshness.max_age_hours * 3)
                           ).isoformat()
    path.write_text(json.dumps(data), encoding="utf-8")
    h = {c["connection"]: c for c in env.manager.health(spec.id)["connections"]}
    assert h["db-b"]["stale"] and not h["files-a"]["stale"]


def test_never_synced_is_stale_not_fresh(env, spec_factory):
    spec = _setup(env, spec_factory)
    h = env.manager.health(spec.id)
    assert h["overall"] == "never_synced"
    assert all(c["stale"] and c["last_success_at"] is None for c in h["connections"])
    assert all(v is None for v in h["totals"].values()), "unknown counts are None, never 0"


def test_counts_and_exclusions_by_rule(env, spec_factory):
    spec = _setup(env, spec_factory)
    excluded = [{"rule": "file_type_not_allowed"}, {"rule": "file_type_not_allowed"},
                {"rule": "over_max_bytes"}]
    blocked = [{"rule": "private_key", "reversal": "remove the key and re-run"}]
    env.service.configure("files-a", excluded_records=excluded, blocked_records=blocked)
    env.manager.sync(spec.id)
    h = {c["connection"]: c for c in env.manager.health(spec.id)["connections"]}
    ex = h["files-a"]["exclusions_by_rule"]
    assert ex["excluded"] == {"file_type_not_allowed": 2, "over_max_bytes": 1}
    assert ex["blocked"] == {"private_key": 1}
    assert h["files-a"]["counts"]["excluded"] == len(excluded)
    assert h["files-a"]["counts"]["blocked"] == len(blocked)
    assert h["files-a"]["counts"]["inventory"] == len(env.service.config["files-a"]["items"])


def test_exclusion_ledgers_are_found_by_pattern(tmp_path):
    """P3: a tagged ledger variant is counted, not silently missed."""
    run = tmp_path / "src" / "r1"
    run.mkdir(parents=True)
    (run / "excluded.jsonl").write_text('{"rule": "a"}\n', encoding="utf-8")
    (run / "excluded.retry.jsonl").write_text('{"rule": "a"}\n{"rule": "b"}\n', encoding="utf-8")
    (run / "report.json").write_text('{"status": "completed"}', encoding="utf-8")
    rep = read_run_reports("src", tmp_path)[0]
    assert rep.exclusions_by_rule["excluded"] == {"a": 2, "b": 1}
    assert rep.counts["excluded"] == 3


def test_missing_counts_are_unknown_not_zero():
    rep = normalize_report({"run_id": "r", "status": "ok", "nodes": 4}, source_id="s")
    assert rep.status == "completed" and rep.counts["nodes"] == 4
    assert rep.counts["edges"] is None and rep.counts["captured"] is None


def test_run_dir_without_report_is_incomplete_not_success(env, spec_factory, tmp_path):
    spec = _setup(env, spec_factory)
    (env.service.root / "files-a" / "zz-in-flight").mkdir(parents=True)
    h = {c["connection"]: c for c in env.manager.health(spec.id)["connections"]}
    assert h["files-a"]["incomplete_runs"] == ["zz-in-flight"]
    assert h["files-a"]["last_success_at"] is None


def test_plan_awaiting_approval_is_visible_in_health(env, spec_factory):
    spec = _setup(env, spec_factory, plans={"files-a": "approved", "db-b": "proposed"})
    env.manager.sync(spec.id)
    h = {c["connection"]: c for c in env.manager.health(spec.id)["connections"]}
    assert h["db-b"]["runnable"] is False and "proposed" in h["db-b"]["skip_reason"]


def _set_finished(env, conn, when):
    rep = read_run_reports(conn, env.service.root)[-1]
    path = env.service.root / conn / rep.run_id / "report.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["finished_at"] = when.isoformat()
    path.write_text(json.dumps(data), encoding="utf-8")


def test_freshness_compares_the_unrounded_age(env, spec_factory):
    """Just past the target is stale even when the displayed (rounded) age equals the target."""
    spec = _setup(env, spec_factory)
    env.manager.sync(spec.id)
    max_age = spec.freshness.max_age_hours
    ended = read_run_reports("files-a", env.service.root)[-1].ended
    env.clock.t = ended + dt.timedelta(hours=max_age, seconds=10)
    h = {c["connection"]: c for c in env.manager.health(spec.id)["connections"]}
    assert h["files-a"]["age_hours"] == round(max_age, 2), "displayed age rounds to the target"
    assert h["files-a"]["stale"] is True
    env.clock.t = ended + dt.timedelta(hours=max_age)
    h = {c["connection"]: c for c in env.manager.health(spec.id)["connections"]}
    assert h["files-a"]["stale"] is False, "exactly at the target is not past it"


def test_a_future_dated_report_is_not_freshness(env, spec_factory):
    from databridge_adapter_agents.manager import FUTURE_SKEW

    spec = _setup(env, spec_factory)
    env.manager.sync(spec.id)
    _set_finished(env, "db-b", env.clock.t + FUTURE_SKEW + dt.timedelta(hours=1))
    h = env.manager.health(spec.id)
    by = {c["connection"]: c for c in h["connections"]}
    assert by["db-b"]["stale"] is True and by["db-b"]["future_timestamp"] is True
    assert by["files-a"]["stale"] is False
    assert any("db-b" in i and "clock or timestamp" in i for i in h["issues"])
    # within the tolerated skew it is fresh, and its age is never negative
    _set_finished(env, "db-b", env.clock.t + FUTURE_SKEW / 2)
    by = {c["connection"]: c for c in env.manager.health(spec.id)["connections"]}
    assert by["db-b"]["stale"] is False and by["db-b"]["age_hours"] == 0


def test_health_totals_are_unknown_when_any_connection_is_unknown(env, spec_factory):
    spec = _setup(env, spec_factory)
    env.service.configure("db-b", no_report=True)          # db-b never has a readable report
    env.manager.sync(spec.id)
    h = env.manager.health(spec.id)
    by = {c["connection"]: c for c in h["connections"]}
    assert by["files-a"]["counts"]["inventory"] is not None
    assert by["db-b"]["counts"]["inventory"] is None
    assert h["totals"]["inventory"] is None, "a partial sum is not a total"


def test_an_unreadable_report_is_unverified_not_failed_and_not_success(tmp_path):
    run = tmp_path / "src" / "r1"
    run.mkdir(parents=True)
    (run / "report.json").write_text("{not json", encoding="utf-8")
    rep = read_run_reports("src", tmp_path)[0]
    assert rep.status == "unverified" and not rep.ok and "unreadable" in rep.error


def test_a_success_recorded_only_by_the_manager_is_not_freshness(env, spec_factory):
    """If the report later disappears, the manager's own 'completed' step does not stand in."""
    import shutil

    spec = _setup(env, spec_factory)
    env.manager.sync(spec.id)
    for d in (env.service.root / "files-a").iterdir():
        shutil.rmtree(d)
    by = {c["connection"]: c for c in env.manager.health(spec.id)["connections"]}
    assert by["files-a"]["last_success_at"] is None and by["files-a"]["stale"] is True
    assert by["db-b"]["last_success_at"] is not None
